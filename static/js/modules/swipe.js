// static/js/modules/swipe.js

// Swipe Navigation State & Logic

// Elements - Assigned from app.js
let contentWrapper = null;
let swipeSections = null;
let numSections = 0;

// State Variables - Index is managed by app.js now via getter/setter
// let currentSectionIndex = 0; // REMOVED - Use getter/setter from app.js
let touchStartX = 0;
let touchStartY = 0;
let touchCurrentX = 0;
let isSwiping = false;
let horizontalSwipeConfirmed = false;
const swipeThreshold = 50;

// --- Add getter/setter provided by app.js ---
let getCurrentSectionIndexCallback = () => 0;
let setCurrentSectionIndexCallback = (index) => {};
// --- End added variables ---

/**
 * Initializes swipe module.
 * @param {HTMLElement} wrapper - The main content wrapper element.
 * @param {NodeList} sections - The NodeList of swipeable sections.
 * @param {function} getIndexFunc - Function from app.js to get current index.
 * @param {function} setIndexFunc - Function from app.js to set current index.
 */
export function initSwipe(wrapper, sections, getIndexFunc, setIndexFunc) { // Added getter/setter args
    contentWrapper = wrapper;
    swipeSections = sections;
    numSections = sections.length;
    getCurrentSectionIndexCallback = getIndexFunc; // Store the getter
    setCurrentSectionIndexCallback = setIndexFunc; // Store the setter
    console.log(`Swipe module initialized with ${numSections} sections.`);
    // Set initial transform without transition using the getter
    updateContentWrapperTransform(getCurrentSectionIndexCallback(), false);
}

// Removed setCurrentSectionIndex and getCurrentSectionIndex exports as state is managed by app.js

/**
 * TouchStart event handler.
 * @param {TouchEvent} event
 */
export function handleTouchStart(event) {
    if (!contentWrapper) return;
    const target = event.target;
    const isInteractive = target.closest('button, input, textarea, select, a, .modal-content');
    const isInsideScrollable = target.closest('.message-area, #agent-status-content, #config-content');
    const isModalOpen = document.querySelector('.modal[style*="display: block"]');

    if (isInteractive || isModalOpen || isInsideScrollable) {
        isSwiping = false;
        console.log(`Swipe ignored (target: ${target.tagName}, interact: ${!!isInteractive}, insideScroll: ${!!isInsideScrollable}, modal: ${!!isModalOpen})`);
        return;
    }
    try {
        touchStartX = event.touches[0].clientX;
        touchStartY = event.touches[0].clientY;
        touchCurrentX = touchStartX;
        isSwiping = true;
        horizontalSwipeConfirmed = false;
        contentWrapper.style.transition = 'none';
        console.log(`TouchStart: startX=${touchStartX.toFixed(0)}`);
    } catch (e) { console.error("Error in touchstart:", e); isSwiping = false; }
}

/**
 * TouchMove event handler.
 * @param {TouchEvent} event
 */
export function handleTouchMove(event) {
    if (!isSwiping || !contentWrapper) return;
    try {
        const currentY = event.touches[0].clientY;
        touchCurrentX = event.touches[0].clientX;
        const diffX = touchCurrentX - touchStartX;
        const diffY = currentY - touchStartY;

        if (!horizontalSwipeConfirmed) {
            if (Math.abs(diffX) > Math.abs(diffY) + 5) {
                horizontalSwipeConfirmed = true; console.log("Horizontal swipe confirmed.");
            } else if (Math.abs(diffY) > Math.abs(diffX) + 5) {
                isSwiping = false; console.log("Vertical scroll detected, canceling swipe."); return;
            }
        }
        if (horizontalSwipeConfirmed) {
            event.preventDefault();
            const currentIdx = getCurrentSectionIndexCallback(); // Get current index
            const baseTranslateXPercent = -currentIdx * 100;
            contentWrapper.style.transform = `translateX(calc(${baseTranslateXPercent}% + ${diffX}px))`;
        }
    } catch (e) { console.error("Error in touchmove:", e); isSwiping = false; }
}

/**
 * TouchEnd event handler.
 * @param {TouchEvent} event
 */
export function handleTouchEnd(event) {
    if (!isSwiping || !contentWrapper) return;
    const wasHorizontal = horizontalSwipeConfirmed;
    isSwiping = false;
    horizontalSwipeConfirmed = false;

    let currentIdx = getCurrentSectionIndexCallback(); // Get index BEFORE potential change
    let finalSectionIndex = currentIdx; // Start with current index

    if (wasHorizontal) {
        const diffX = touchCurrentX - touchStartX;
        console.log(`TouchEnd (H): Index BEFORE=${currentIdx}, diffX=${diffX.toFixed(0)}, thres=${swipeThreshold}`);

        if (Math.abs(diffX) > swipeThreshold) {
            if (diffX < 0 && currentIdx < numSections - 1) { // Swipe Left
                finalSectionIndex++; console.log("Swipe Left -> Proposed Index:", finalSectionIndex);
            } else if (diffX > 0 && currentIdx > 0) { // Swipe Right
                finalSectionIndex--; console.log("Swipe Right -> Proposed Index:", finalSectionIndex);
            } else { console.log("Swipe threshold met but at boundary."); }
        } else { console.log("Swipe distance below threshold."); }
    } else {
         console.log("TouchEnd: No horizontal swipe confirmed. Snapping back.");
    }

    // Clamp index just in case
    finalSectionIndex = Math.max(0, Math.min(numSections - 1, finalSectionIndex));

    // Update index in main app.js state using the callback
    setCurrentSectionIndexCallback(finalSectionIndex);
    console.log(`TouchEnd: Set main index to: ${finalSectionIndex}`);

    // Trigger the visual update using the *final* index
    updateContentWrapperTransform(finalSectionIndex, true);
}


/**
 * Updates the CSS transform. Now takes index directly.
 * @param {number} index - The index of the section to display.
 * @param {boolean} [useTransition=true]
 */
export function updateContentWrapperTransform(index, useTransition = true) {
    if (contentWrapper && typeof index === 'number' && index >= 0 && index < numSections) {
        const newTranslateXPercent = -index * 100;
        console.log(`Updating transform: Target Index=${index}, TranslateX=${newTranslateXPercent}% (transition: ${useTransition})`);
        contentWrapper.style.transition = useTransition ? 'transform 0.3s ease-in-out' : 'none';
        contentWrapper.style.transform = `translateX(${newTranslateXPercent}%)`;
    } else {
        console.error(`Invalid call to updateContentWrapperTransform: Index=${index}, Wrapper=${!!contentWrapper}`);
        // As a fallback, ensure it snaps back to the current official index if call is bad
        if(contentWrapper) {
             const fallbackIndex = getCurrentSectionIndexCallback();
             const fallbackTranslate = -fallbackIndex * 100;
             console.warn(`Fallback: Snapping to index ${fallbackIndex} (${fallbackTranslate}%)`);
             contentWrapper.style.transition = 'transform 0.3s ease-in-out'; // Ensure transition for snap back
             contentWrapper.style.transform = `translateX(${fallbackTranslate}%)`;
        }
    }
}

/**
 * Adds keyboard navigation listeners.
 * @param {function} getIndex - Function to get the current section index from app.js.
 * @param {function} setIndex - Function to set the new section index in app.js.
 */
export function addKeyboardNavListeners(getIndex, setIndex) {
     document.addEventListener('keydown', (e) => {
        const targetTagName = document.activeElement?.tagName.toLowerCase();
        const isModalOpen = document.querySelector('.modal[style*="display: block"]');
        const isInputFocused = ['textarea', 'input', 'select'].includes(targetTagName);
        if (isModalOpen || isInputFocused) return;

        let currentIndex = getIndex(); // Get current index

        if (e.key === 'ArrowLeft' && currentIndex > 0) {
            const newIndex = currentIndex - 1;
            console.log(`Key Left -> New Index: ${newIndex}`);
            setIndex(newIndex); // Update state
            updateContentWrapperTransform(newIndex, true); // Update view
        } else if (e.key === 'ArrowRight' && currentIndex < numSections - 1) {
            const newIndex = currentIndex + 1;
             console.log(`Key Right -> New Index: ${newIndex}`);
            setIndex(newIndex); // Update state
            updateContentWrapperTransform(newIndex, true); // Update view
        }
    });
    console.log("Keyboard navigation listeners added.");
}
